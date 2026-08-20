from django.test import TestCase
from rest_framework.test import APIClient

from apps.factories import create_admin, create_category, create_product, create_user
from apps.products.models import Review, WishlistItem

CATEGORIES_URL = '/api/v1/products/categories/'
PRODUCTS_URL = '/api/v1/products/products/'
WISHLIST_URL = '/api/v1/products/wishlist/'


def names(response):
    payload = response.data['results'] if isinstance(response.data, dict) else response.data
    return [entry['name'] for entry in payload]


class CategoryViewSetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        create_category(name='Bakery')

    def test_anyone_can_list_categories(self):
        response = self.client.get(CATEGORIES_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(names(response), ['Bakery'])

    def test_customers_cannot_create_categories(self):
        self.client.force_authenticate(create_user())

        response = self.client.post(CATEGORIES_URL, {'name': 'Frozen', 'slug': 'frozen'}, format='json')

        self.assertEqual(response.status_code, 403)

    def test_staff_can_create_categories(self):
        self.client.force_authenticate(create_admin())

        response = self.client.post(CATEGORIES_URL, {'name': 'Frozen', 'slug': 'frozen'}, format='json')

        self.assertEqual(response.status_code, 201)


class ProductQuerysetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.pantry = create_category(name='Pantry')
        self.drinks = create_category(name='Drinks')
        self.mealie = create_product(category=self.pantry, name='Maize Meal', price='4.50', brand='Gold')
        self.cola = create_product(category=self.drinks, name='Cola Bottle', price='1.25', brand='Fizz', is_featured=True)
        self.hidden = create_product(category=self.drinks, name='Hidden Juice', price='2.00', is_active=False)

    def test_inactive_products_are_hidden_from_customers(self):
        response = self.client.get(PRODUCTS_URL)

        self.assertNotIn('Hidden Juice', names(response))

    def test_staff_see_inactive_products(self):
        self.client.force_authenticate(create_admin())

        response = self.client.get(PRODUCTS_URL)

        self.assertIn('Hidden Juice', names(response))

    def test_filter_by_category_slug(self):
        response = self.client.get(PRODUCTS_URL, {'category': 'drinks'})

        self.assertEqual(names(response), ['Cola Bottle'])

    def test_filter_by_brand_is_case_insensitive(self):
        response = self.client.get(PRODUCTS_URL, {'brand': 'gold'})

        self.assertEqual(names(response), ['Maize Meal'])

    def test_search_matches_name_or_description(self):
        self.assertEqual(names(self.client.get(PRODUCTS_URL, {'search': 'cola'})), ['Cola Bottle'])
        self.assertEqual(names(self.client.get(PRODUCTS_URL, {'search': 'Maize Meal description'})), ['Maize Meal'])

    def test_filter_by_price_range(self):
        self.assertEqual(names(self.client.get(PRODUCTS_URL, {'min_price': '2.00'})), ['Maize Meal'])
        self.assertEqual(names(self.client.get(PRODUCTS_URL, {'max_price': '2.00'})), ['Cola Bottle'])

    def test_allowed_ordering_is_applied(self):
        response = self.client.get(PRODUCTS_URL, {'ordering': 'price'})

        self.assertEqual(names(response), ['Cola Bottle', 'Maize Meal'])

    def test_unknown_ordering_falls_back_to_newest_first(self):
        response = self.client.get(PRODUCTS_URL, {'ordering': 'stock_quantity'})

        self.assertEqual(names(response), ['Cola Bottle', 'Maize Meal'])

    def test_featured_action_returns_only_featured_products(self):
        response = self.client.get(f'{PRODUCTS_URL}featured/')

        self.assertEqual(names(response), ['Cola Bottle'])

    def test_recent_action_returns_newest_first(self):
        response = self.client.get(f'{PRODUCTS_URL}recent/')

        self.assertEqual(names(response), ['Cola Bottle', 'Maize Meal'])

    def test_products_are_looked_up_by_slug(self):
        response = self.client.get(f'{PRODUCTS_URL}maize-meal/')

        self.assertEqual(response.data['name'], 'Maize Meal')


class ProductReviewActionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.product = create_product(name='Maize Meal')
        self.user = create_user()

    def test_reviews_are_publicly_readable(self):
        Review.objects.create(product=self.product, user=self.user, rating=5, comment='Great')

        response = self.client.get(f'{PRODUCTS_URL}maize-meal/reviews/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['comment'], 'Great')
        self.assertEqual(response.data[0]['user'], self.user.email)

    def test_anonymous_users_cannot_post_reviews(self):
        response = self.client.post(f'{PRODUCTS_URL}maize-meal/reviews/', {'rating': 4}, format='json')

        self.assertEqual(response.status_code, 401)

    def test_posting_twice_updates_the_existing_review(self):
        self.client.force_authenticate(self.user)

        self.client.post(f'{PRODUCTS_URL}maize-meal/reviews/', {'rating': 3, 'comment': 'Fine'}, format='json')
        response = self.client.post(f'{PRODUCTS_URL}maize-meal/reviews/', {'rating': 5, 'comment': 'Better'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Review.objects.filter(product=self.product, user=self.user).count(), 1)
        self.assertEqual(response.data['rating'], 5)

    def test_invalid_rating_is_rejected(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(f'{PRODUCTS_URL}maize-meal/reviews/', {'rating': 0}, format='json')

        self.assertEqual(response.status_code, 400)


class WishlistViewSetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.product = create_product(name='Maize Meal')

    def test_authentication_is_required(self):
        self.assertEqual(self.client.get(WISHLIST_URL).status_code, 401)

    def test_wishlist_items_are_created_for_the_current_user(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(WISHLIST_URL, {'product_id': self.product.pk}, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(WishlistItem.objects.get().user, self.user)

    def test_customers_only_see_their_own_wishlist(self):
        other = create_user(username='other', phone_number='0770000002')
        WishlistItem.objects.create(user=other, product=self.product)
        self.client.force_authenticate(self.user)

        response = self.client.get(WISHLIST_URL)

        self.assertEqual(response.data['count'], 0)
