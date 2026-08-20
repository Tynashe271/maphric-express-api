from django.test import TestCase

from apps.accounts.serializers import RegisterSerializer, UserSerializer
from apps.factories import create_user


VALID_PAYLOAD = {
    'username': 'newshopper',
    'email': 'New.Shopper@Example.com',
    'phone_number': '077 123 4568',
    'password': 'str0ng-passw0rd!',
    'password2': 'str0ng-passw0rd!',
    'first_name': 'New',
    'last_name': 'Shopper',
}


class RegisterSerializerTests(TestCase):
    def test_creates_user_with_normalised_email_and_phone(self):
        serializer = RegisterSerializer(data=VALID_PAYLOAD)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()

        self.assertEqual(user.email, 'new.shopper@example.com')
        self.assertEqual(user.phone_number, '0771234568')
        self.assertTrue(user.check_password('str0ng-passw0rd!'))

    def test_rejects_short_phone_number(self):
        serializer = RegisterSerializer(data={**VALID_PAYLOAD, 'phone_number': '077-12'})
        self.assertFalse(serializer.is_valid())
        self.assertIn('phone_number', serializer.errors)

    def test_rejects_duplicate_phone_number(self):
        create_user(username='existing', phone_number='0771234568')
        serializer = RegisterSerializer(data=VALID_PAYLOAD)
        self.assertFalse(serializer.is_valid())
        self.assertIn('phone_number', serializer.errors)

    def test_rejects_duplicate_email_case_insensitively(self):
        create_user(username='existing', email='new.shopper@example.com', phone_number='0770000001')
        serializer = RegisterSerializer(data=VALID_PAYLOAD)
        self.assertFalse(serializer.is_valid())
        self.assertIn('email', serializer.errors)

    def test_rejects_mismatched_passwords(self):
        serializer = RegisterSerializer(data={**VALID_PAYLOAD, 'password2': 'another-passw0rd!'})
        self.assertFalse(serializer.is_valid())
        self.assertIn('password', serializer.errors)

    def test_rejects_weak_password(self):
        serializer = RegisterSerializer(data={**VALID_PAYLOAD, 'password': '123', 'password2': '123'})
        self.assertFalse(serializer.is_valid())
        self.assertIn('password', serializer.errors)


class UserSerializerTests(TestCase):
    def test_privileged_fields_are_read_only(self):
        user = create_user()
        serializer = UserSerializer(user, data={'email_verified': True, 'is_staff': True}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated = serializer.save()

        self.assertFalse(updated.email_verified)
        self.assertFalse(updated.is_staff)


class UserModelTests(TestCase):
    def test_str_prefers_email_and_falls_back_to_username(self):
        with_email = create_user(username='withemail')
        self.assertEqual(str(with_email), 'withemail@example.com')

        without_email = create_user(username='noemail', email='')
        self.assertEqual(str(without_email), 'noemail')
