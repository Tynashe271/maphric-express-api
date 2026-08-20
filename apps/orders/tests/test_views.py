import json
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.cart.models import CartItem
from apps.factories import (
    create_admin,
    create_cart_item,
    create_order,
    create_order_item,
    create_product,
    create_user,
)
from apps.orders.models import AdminActivity, DeliverySettings, Order, TransactionArchive
from apps.products.models import Review

ORDERS_URL = '/api/v1/orders/'
CHECKOUT_URL = f'{ORDERS_URL}checkout/'
DELIVERY_SETTINGS_URL = f'{ORDERS_URL}delivery-settings/'
WIPE_URL = f'{ORDERS_URL}wipe-transactions/'
ARCHIVES_URL = f'{ORDERS_URL}transaction-archives/'

SHIPPING = {
    'shipping_name': 'Test Shopper',
    'shipping_phone': '0771234567',
    'shipping_address': '1 Bradfield Road, Bulawayo',
}


class OrderListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.other = create_user(username='other', phone_number='0770000002')
        create_order(self.user)
        create_order(self.other)

    def test_authentication_is_required(self):
        self.assertEqual(self.client.get(ORDERS_URL).status_code, 401)

    def test_customers_only_see_their_own_orders(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(ORDERS_URL)

        self.assertEqual(response.data['count'], 1)

    def test_staff_see_every_order(self):
        self.client.force_authenticate(create_admin())

        response = self.client.get(ORDERS_URL)

        self.assertEqual(response.data['count'], 2)

    def test_orders_are_read_only(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(ORDERS_URL, SHIPPING, format='json')

        self.assertEqual(response.status_code, 405)


class CheckoutTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.product = create_product(price='4.50', stock_quantity=5)
        self.client.force_authenticate(self.user)

    def test_checkout_requires_shipping_details(self):
        create_cart_item(self.user, self.product, quantity=1)

        response = self.client.post(CHECKOUT_URL, {}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_empty_cart_cannot_be_checked_out(self):
        response = self.client.post(CHECKOUT_URL, SHIPPING, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'Your cart is empty.')

    def test_quantity_above_stock_is_rejected(self):
        create_cart_item(self.user, self.product, quantity=6)

        response = self.client.post(CHECKOUT_URL, SHIPPING, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'One or more products are out of stock.')

    def test_checkout_creates_order_reduces_stock_and_clears_cart(self):
        create_cart_item(self.user, self.product, quantity=2)

        response = self.client.post(CHECKOUT_URL, SHIPPING, format='json')

        self.assertEqual(response.status_code, 201)
        order = Order.objects.get()
        self.assertEqual(order.total, Decimal('9.00'))
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual([item.product_name for item in order.items.all()], ['Maize Meal'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 3)
        self.assertFalse(CartItem.objects.filter(user=self.user).exists())

    def test_delivery_fee_is_added_below_the_free_delivery_threshold(self):
        DeliverySettings.objects.create(pk=1, delivery_fee=Decimal('3.00'), free_delivery_threshold=Decimal('50.00'))
        create_cart_item(self.user, self.product, quantity=2)

        response = self.client.post(CHECKOUT_URL, SHIPPING, format='json')

        self.assertEqual(response.data['total'], '12.00')

    def test_delivery_is_free_above_the_threshold(self):
        DeliverySettings.objects.create(pk=1, delivery_fee=Decimal('3.00'), free_delivery_threshold=Decimal('5.00'))
        create_cart_item(self.user, self.product, quantity=2)

        response = self.client.post(CHECKOUT_URL, SHIPPING, format='json')

        self.assertEqual(response.data['total'], '9.00')


class SetStatusTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.order = create_order(self.user)

    def test_customers_cannot_change_status(self):
        self.client.force_authenticate(self.user)

        response = self.client.patch(f'{ORDERS_URL}{self.order.pk}/set-status/', {'status': 'paid'}, format='json')

        self.assertEqual(response.status_code, 403)

    def test_unknown_status_is_rejected(self):
        self.client.force_authenticate(create_admin())

        response = self.client.patch(f'{ORDERS_URL}{self.order.pk}/set-status/', {'status': 'teleported'}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_status_change_is_saved_and_logged(self):
        admin = create_admin()
        self.client.force_authenticate(admin)

        response = self.client.patch(f'{ORDERS_URL}{self.order.pk}/set-status/', {'status': 'SHIPPED'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.SHIPPED)
        activity = AdminActivity.objects.get(action='order_status_updated')
        self.assertEqual(activity.actor, admin)
        self.assertEqual(activity.metadata['previous_status'], 'pending')


class DeliverySettingsTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_settings_are_created_on_first_read(self):
        response = self.client.get(DELIVERY_SETTINGS_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['estimated_minutes'], 55)
        self.assertTrue(DeliverySettings.objects.filter(pk=1).exists())

    def test_customers_cannot_update_settings(self):
        self.client.force_authenticate(create_user())

        response = self.client.put(DELIVERY_SETTINGS_URL, {'delivery_fee': '2.00'}, format='json')

        self.assertEqual(response.status_code, 403)

    def test_staff_can_update_settings(self):
        admin = create_admin()
        self.client.force_authenticate(admin)

        response = self.client.put(
            DELIVERY_SETTINGS_URL,
            {'delivery_fee': '2.50', 'estimated_minutes': 40, 'opening_hours': 'Monday to Friday'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        record = DeliverySettings.objects.get(pk=1)
        self.assertEqual(record.delivery_fee, Decimal('2.50'))
        self.assertEqual(record.estimated_minutes, 40)
        self.assertEqual(record.updated_by, admin)
        self.assertTrue(AdminActivity.objects.filter(action='delivery_settings_updated').exists())

    def test_invalid_values_are_reported(self):
        self.client.force_authenticate(create_admin())

        response = self.client.put(DELIVERY_SETTINGS_URL, {'estimated_minutes': 'soon'}, format='json')

        self.assertEqual(response.status_code, 400)


class WipeTransactionsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = create_admin()
        self.user = create_user()
        self.product = create_product(stock_quantity=1)
        self.order = create_order(self.user, total='9.00')
        create_order_item(self.order, self.product, quantity=2)

    def test_confirmation_phrase_is_required(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(WIPE_URL, {'confirmation': 'wipe'}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Order.objects.exists())

    def test_customers_cannot_wipe_transactions(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(WIPE_URL, {'confirmation': 'WIPE ALL TRANSACTIONS'}, format='json')

        self.assertEqual(response.status_code, 403)

    def test_orders_are_archived_stock_restored_and_activity_logged(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(WIPE_URL, {'confirmation': 'WIPE ALL TRANSACTIONS'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['deleted'], 1)
        self.assertFalse(Order.objects.exists())
        archive = TransactionArchive.objects.get(pk=response.data['archive_id'])
        self.assertEqual(archive.transaction_count, 1)
        self.assertEqual(archive.total_amount, Decimal('9.00'))
        self.assertEqual(archive.data[0]['shipping_name'], 'Test Shopper')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 3)
        self.assertTrue(AdminActivity.objects.filter(action='transactions_archived').exists())


class TransactionArchiveTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = create_admin(first_name='Ada', last_name='Manager')
        self.archive = TransactionArchive.objects.create(
            created_by=self.admin,
            transaction_count=1,
            total_amount=Decimal('9.00'),
            data=[{
                'id': 7,
                'status': 'paid',
                'total': '9.00',
                'shipping_name': 'Test Shopper',
                'shipping_phone': '0771234567',
                'payment_method': 'EcoCash',
                'payment_status': 'paid',
                'items': [{'product_name': 'Maize Meal', 'quantity': 2, 'unit_price': '4.50'}],
            }],
        )
        self.client.force_authenticate(self.admin)

    def test_customers_cannot_list_archives(self):
        self.client.force_authenticate(create_user())

        self.assertEqual(self.client.get(ARCHIVES_URL).status_code, 403)

    def test_archives_are_listed_with_creator_name(self):
        response = self.client.get(ARCHIVES_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['created_by'], 'Ada Manager')

    def test_archives_can_be_filtered_by_id_and_date_range(self):
        created_date = self.archive.created_at.date().isoformat()

        self.assertEqual(len(self.client.get(ARCHIVES_URL, {'q': str(self.archive.pk)}).data), 1)
        self.assertEqual(len(self.client.get(ARCHIVES_URL, {'q': '99999'}).data), 0)
        self.assertEqual(len(self.client.get(ARCHIVES_URL, {'from': created_date, 'to': created_date}).data), 1)
        self.assertEqual(len(self.client.get(ARCHIVES_URL, {'from': '2999-01-01'}).data), 0)

    def test_csv_export_contains_the_order_row(self):
        response = self.client.get(f'{ARCHIVES_URL}{self.archive.pk}/csv/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.decode()
        self.assertIn('MAP-000007', body)
        self.assertIn('Test Shopper', body)

    def test_pdf_export_returns_a_pdf_attachment(self):
        response = self.client.get(f'{ARCHIVES_URL}{self.archive.pk}/pdf/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))

    def test_pdf_export_spans_multiple_pages_for_long_archives(self):
        self.archive.data = [
            {
                'id': index,
                'status': 'paid',
                'total': '9.00',
                'shipping_name': f'Shopper {index}',
                'shipping_phone': '0771234567',
                'payment_method': 'EcoCash',
                'payment_status': 'paid',
                'items': [{'product_name': 'Maize Meal', 'quantity': 1, 'unit_price': '4.50'}],
            }
            for index in range(1, 40)
        ]
        self.archive.save(update_fields=['data'])

        response = self.client.get(f'{ARCHIVES_URL}{self.archive.pk}/pdf/')

        self.assertEqual(response.status_code, 200)
        self.assertGreater(b''.join(response.streaming_content).count(b'/Type /Page\n'), 1)

    def test_missing_archive_returns_not_found(self):
        self.assertEqual(self.client.get(f'{ARCHIVES_URL}999/csv/').status_code, 404)
        self.assertEqual(self.client.get(f'{ARCHIVES_URL}999/pdf/').status_code, 404)


class AdminReportingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = create_admin()
        self.user = create_user()
        self.product = create_product(stock_quantity=2)
        self.order = create_order(self.user, total='9.00', payment_status='paid', status=Order.Status.PAID)
        create_order_item(self.order, self.product, quantity=1)
        Review.objects.create(product=self.product, user=self.user, rating=4, comment='Good')
        AdminActivity.objects.create(actor=self.admin, action='order_status_updated', description='Changed status.')
        self.client.force_authenticate(self.admin)

    def test_summary_aggregates_revenue_statuses_and_low_stock(self):
        response = self.client.get(f'{ORDERS_URL}admin-summary/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['revenue'], Decimal('9.00'))
        self.assertEqual(response.data['transactions'], 1)
        self.assertEqual(response.data['payment_status'][0]['payment_status'], 'paid')
        self.assertEqual(response.data['order_status'][0]['status'], 'paid')
        self.assertEqual(response.data['low_stock'][0]['name'], 'Maize Meal')

    def test_summary_handles_an_empty_store(self):
        Review.objects.all().delete()
        self.order.items.all().delete()
        Order.objects.all().delete()

        response = self.client.get(f'{ORDERS_URL}admin-summary/')

        self.assertEqual(response.data['revenue'], 0)
        self.assertEqual(response.data['transactions'], 0)

    def test_activity_feed_lists_actions(self):
        response = self.client.get(f'{ORDERS_URL}admin-activity/')

        self.assertEqual(response.data[0]['action'], 'order_status_updated')
        self.assertEqual(response.data[0]['actor'], self.admin.username)

    def test_reviews_feed_lists_customer_reviews(self):
        response = self.client.get(f'{ORDERS_URL}admin-reviews/')

        self.assertEqual(response.data[0]['product'], 'Maize Meal')
        self.assertEqual(response.data[0]['rating'], 4)

    def test_backup_returns_orders_archives_and_products(self):
        response = self.client.get(f'{ORDERS_URL}backup/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        payload = json.loads(response.content.decode())
        self.assertEqual(len(payload['orders']), 1)
        self.assertEqual(payload['archives'], [])
        self.assertEqual(payload['products'][0]['name'], 'Maize Meal')
        self.assertTrue(AdminActivity.objects.filter(action='backup_downloaded').exists())

    def test_reporting_endpoints_are_staff_only(self):
        self.client.force_authenticate(self.user)

        for path in ('admin-summary', 'admin-activity', 'admin-reviews', 'backup'):
            self.assertEqual(self.client.get(f'{ORDERS_URL}{path}/').status_code, 403, path)
