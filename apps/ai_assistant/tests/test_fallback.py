from django.test import TestCase

from apps.ai_assistant.views import ShoppingAssistantView
from apps.factories import create_category, create_order, create_product, create_user
from apps.orders.models import Order

fallback = ShoppingAssistantView.catalogue_fallback


class CatalogueFallbackTests(TestCase):
    def setUp(self):
        self.pantry = create_category(name='Pantry')
        self.mealie = create_product(category=self.pantry, name='Maize Meal', price='4.50', stock_quantity=10)
        self.cola = create_product(category=self.pantry, name='Cola Bottle', price='1.25', stock_quantity=4)
        self.sold_out = create_product(category=self.pantry, name='Cooking Oil', price='6.00', stock_quantity=0)
        self.products = [self.mealie, self.cola, self.sold_out]

    def test_delivery_questions_explain_local_delivery(self):
        answer = fallback('When will my shipping arrive?', self.products, [])

        self.assertIn('local delivery from Bradfield', answer)

    def test_order_question_without_orders(self):
        answer = fallback('What is my order status?', self.products, [])

        self.assertEqual(answer, 'You have no recent orders yet. Completed checkouts will appear in Order history.')

    def test_order_question_reports_the_latest_order(self):
        order = create_order(create_user(), total='9.00', status=Order.Status.SHIPPED)

        answer = fallback('track my order', self.products, [order])

        self.assertIn(f'HHB-{order.id:06d}', answer)
        self.assertIn('shipped', answer)
        self.assertIn('9.00', answer)

    def test_budget_question_lists_affordable_in_stock_products(self):
        answer = fallback('What can I buy for $5?', self.products, [])

        self.assertIn('Cola Bottle', answer)
        self.assertIn('Maize Meal', answer)
        self.assertNotIn('Cooking Oil', answer)

    def test_budget_question_without_affordable_products(self):
        answer = fallback('I want to spend $1', self.products, [])

        self.assertEqual(answer, 'There are currently no in-stock products priced at USD 1.00 or less.')

    def test_product_name_question_reports_price_and_stock(self):
        answer = fallback('Do you have maize meal?', self.products, [])

        self.assertIn('Maize Meal — USD 4.50 (10 in stock)', answer)

    def test_out_of_stock_products_are_labelled(self):
        answer = fallback('cooking oil', self.products, [])

        self.assertIn('Cooking Oil — USD 6.00 (out of stock)', answer)

    def test_stock_question_lists_available_products(self):
        answer = fallback('Which items are in stock?', self.products, [])

        self.assertIn('Currently in stock:', answer)
        self.assertNotIn('Cooking Oil', answer)

    def test_stock_question_when_nothing_is_available(self):
        answer = fallback('Which items are in stock?', [self.sold_out], [])

        self.assertIn('No groceries are currently marked as in stock', answer)

    def test_stock_question_mentions_remaining_products_beyond_twelve(self):
        extra = [
            create_product(category=self.pantry, name=f'Item Number {index}', price='2.00', stock_quantity=3)
            for index in range(12)
        ]

        answer = fallback('Which items are in stock?', [self.mealie, self.cola] + extra, [])

        self.assertIn('There are 2 more in-stock products.', answer)

    def test_unrelated_question_returns_guidance(self):
        answer = fallback('Hello there', self.products, [])

        self.assertIn('I can help with current products', answer)
