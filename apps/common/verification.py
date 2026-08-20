"""Twilio Verify client used by the password recovery flow."""

import base64
import json
from urllib import error as urlerror, parse, request as urlrequest

from django.conf import settings

VERIFY_BASE_URL = 'https://verify.twilio.com/v2/Services'
REQUEST_TIMEOUT = 10


class VerificationError(Exception):
    """Raised when Twilio Verify cannot be reached or rejects a request."""

    def __init__(self, detail, provider_message=''):
        super().__init__(detail)
        self.detail = detail
        self.provider_message = provider_message


def is_configured():
    return all((
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_AUTH_TOKEN,
        settings.TWILIO_VERIFY_SERVICE_SID,
    ))


def _post(endpoint, payload, http_detail, connection_detail):
    body = parse.urlencode(payload).encode()
    api_request = urlrequest.Request(
        f'{VERIFY_BASE_URL}/{settings.TWILIO_VERIFY_SERVICE_SID}/{endpoint}',
        data=body,
        method='POST',
    )
    credentials = f'{settings.TWILIO_ACCOUNT_SID}:{settings.TWILIO_AUTH_TOKEN}'.encode()
    api_request.add_header('Authorization', f'Basic {base64.b64encode(credentials).decode()}')
    try:
        raw = urlrequest.urlopen(api_request, timeout=REQUEST_TIMEOUT).read().decode()
    except urlerror.HTTPError as http_error:
        try:
            provider_message = json.loads(http_error.read().decode()).get('message', '')
        except (json.JSONDecodeError, UnicodeDecodeError):
            provider_message = ''
        raise VerificationError(http_detail, provider_message) from http_error
    except Exception as api_error:
        raise VerificationError(connection_detail) from api_error
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def send_code(phone, channel):
    """Ask Twilio to deliver a verification code over SMS or WhatsApp."""
    _post(
        'Verifications',
        {'To': phone, 'Channel': channel},
        'The text message could not be sent. Choose email or try again.',
        'The verification service could not connect. Choose email or try again.',
    )


def check_code(phone, code):
    """Return True when Twilio approves the supplied code."""
    result = _post(
        'VerificationCheck',
        {'To': phone, 'Code': code},
        'The text verification service is unavailable. Try again.',
        'The text verification service is unavailable. Try again.',
    )
    return result.get('status') == 'approved'
