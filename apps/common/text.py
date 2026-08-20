"""Formatting and normalisation helpers shared by the API apps."""

ORDER_REFERENCE_PREFIX = 'MAP'
LOCAL_DIALLING_CODE = '+263'


def normalize_phone_number(value):
    """Strip everything except digits and a leading-style plus sign."""
    return ''.join(character for character in str(value) if character.isdigit() or character == '+')


def to_international_phone_number(phone):
    """Convert a local 0-prefixed number to its international form."""
    return f'{LOCAL_DIALLING_CODE}{phone[1:]}' if phone.startswith('0') else phone


def format_order_reference(order_id):
    """Render the customer-facing order number, for example MAP-000123."""
    return f'{ORDER_REFERENCE_PREFIX}-{int(order_id):06d}'


def mask_email(email, visible=2, fallback='your registered email'):
    """Hide most of the local part of an email address."""
    local, _, domain = str(email).partition('@')
    if not domain:
        return fallback
    return f'{local[:visible]}***@{domain}'


def display_name(user, default='System'):
    """Human readable name for a possibly missing user."""
    if not user:
        return default
    return user.get_full_name() or user.username
