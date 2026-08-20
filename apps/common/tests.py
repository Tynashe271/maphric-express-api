from django.test import SimpleTestCase

from apps.common.text import (
    display_name,
    format_order_reference,
    mask_email,
    normalize_phone_number,
    to_international_phone_number,
)


class TextHelperTests(SimpleTestCase):
    def test_normalize_phone_number_keeps_digits_and_plus(self):
        self.assertEqual(normalize_phone_number(' +263 (77) 291-0496 '), '+263772910496')
        self.assertEqual(normalize_phone_number(772910496), '772910496')

    def test_to_international_phone_number_only_rewrites_local_numbers(self):
        self.assertEqual(to_international_phone_number('0772910496'), '+263772910496')
        self.assertEqual(to_international_phone_number('+263772910496'), '+263772910496')

    def test_format_order_reference_pads_to_six_digits(self):
        self.assertEqual(format_order_reference(123), 'MAP-000123')
        self.assertEqual(format_order_reference('7'), 'MAP-000007')

    def test_mask_email_hides_local_part(self):
        self.assertEqual(mask_email('customer@example.com'), 'cu***@example.com')
        self.assertEqual(mask_email('customer@example.com', visible=1), 'c***@example.com')
        self.assertEqual(mask_email('not-an-email'), 'your registered email')

    def test_display_name_falls_back_to_username_then_default(self):
        class FakeUser:
            def __init__(self, full_name, username):
                self.full_name = full_name
                self.username = username

            def get_full_name(self):
                return self.full_name

        self.assertEqual(display_name(FakeUser('Ada Lovelace', 'ada')), 'Ada Lovelace')
        self.assertEqual(display_name(FakeUser('', 'ada')), 'ada')
        self.assertEqual(display_name(None), 'System')
