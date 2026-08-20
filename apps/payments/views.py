import logging
import os
from paynow import Paynow
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.common.querysets import scope_to_user
from apps.common.responses import bad_gateway, error_response, not_found, service_unavailable
from apps.common.text import format_order_reference, normalize_phone_number
from apps.orders.models import Order

logger = logging.getLogger('maphric.payments')

TRUE_VALUES = {'1', 'true', 'yes', 'on'}


class GatewayNotConfigured(RuntimeError):
    """Raised when the Paynow merchant credentials are missing."""


def env(name, default=''):
    return os.getenv(name, default).strip()


def env_flag(name, default='False'):
    return env(name, default).lower() in TRUE_VALUES


def gateway():
    integration_id = env('PAYNOW_INTEGRATION_ID')
    integration_key = env('PAYNOW_INTEGRATION_KEY')
    if not integration_id or not integration_key:
        raise GatewayNotConfigured('EcoCash merchant payments are not configured.')
    return Paynow(
        integration_id,
        integration_key,
        env('PAYNOW_RETURN_URL'),
        env('PAYNOW_RESULT_URL'),
    )


class InitiatePaymentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        orders = scope_to_user(Order.objects.all(), request.user)
        order = orders.filter(pk=request.data.get('order_id')).first()
        if not order:
            return not_found('Order not found.')
        if order.payment_status == 'paid':
            return Response({'paid': True, 'status': 'paid'})
        method = str(request.data.get('method', '')).lower()
        if method != 'ecocash':
            return error_response('This endpoint currently accepts EcoCash payments.')
        phone = normalize_phone_number(request.data.get('phone', ''))
        if not phone:
            return error_response('Enter the EcoCash phone number.')
        test_mode = env_flag('PAYNOW_TEST_MODE')
        if test_mode:
            phone = '0771111111'
        try:
            paynow = gateway()
        except GatewayNotConfigured:
            logger.error('Paynow credentials are missing; order %s cannot be paid.', order.id)
            return service_unavailable(
                'EcoCash merchant payments are not configured. Choose another payment method.'
            )
        auth_email = (
            env('PAYNOW_AUTH_EMAIL')
            or order.user.email
            or request.user.email
            or ''
        ).strip()
        if not auth_email:
            return error_response('The order account needs an email address before EcoCash payment.')
        reference = format_order_reference(order.id)
        try:
            payment = paynow.create_payment(reference, auth_email)
            payment.add(f'Maphric Express order {reference}', float(order.total))
            response = paynow.send_mobile(payment, phone, 'ecocash')
        except Exception:
            logger.exception('Paynow mobile payment request failed for order %s.', order.id)
            return bad_gateway(
                'EcoCash could not be contacted. Please try again or choose another payment method.'
            )
        if not getattr(response, 'success', False):
            logger.warning(
                'Paynow rejected the payment for order %s: %s',
                order.id,
                getattr(response, 'data', {}) or {},
            )
            return error_response(
                'EcoCash rejected the payment request. Check the number or choose another payment method.'
            )
        poll_url = getattr(response, 'poll_url', '')
        if not poll_url:
            logger.error('Paynow accepted order %s without returning a poll URL.', order.id)
            return bad_gateway('Paynow accepted the request but did not return a payment status URL.')
        order.payment_method = 'EcoCash'
        order.payment_status = 'pending'
        order.paynow_poll_url = poll_url
        order.save(update_fields=['payment_method', 'payment_status', 'paynow_poll_url', 'updated_at'])
        return Response({
            'paid': False,
            'status': 'pending',
            'test_mode': test_mode,
            'message': 'Paynow test success is being simulated.' if test_mode else 'Approve the EcoCash prompt on your phone.',
        })


class PaymentStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, order_id):
        order = Order.objects.filter(pk=order_id, user=request.user).first()
        if not order:
            return not_found('Order not found.')
        if order.payment_status == 'paid':
            return Response({'paid': True, 'status': 'paid'})
        if not order.paynow_poll_url:
            return Response({'paid': False, 'status': order.payment_status})
        try:
            payment_status = gateway().check_transaction_status(order.paynow_poll_url)
        except GatewayNotConfigured:
            logger.error('Paynow credentials are missing; order %s payment status is unknown.', order.id)
            return service_unavailable('EcoCash merchant payments are not configured.')
        except Exception:
            logger.exception('Paynow status check failed for order %s.', order.id)
            return bad_gateway('The payment status could not be checked. Please try again shortly.')
        if payment_status.paid:
            order.payment_status = 'paid'
            order.status = Order.Status.PAID
            order.paynow_reference = getattr(payment_status, 'paynow_reference', '') or ''
            order.save(update_fields=['payment_status', 'status', 'paynow_reference', 'updated_at'])
        return Response({'paid': bool(payment_status.paid), 'status': payment_status.status})


class PaymentCallbackView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        # Payment state is independently verified through Paynow's signed poll
        # URL before an order is marked paid.
        return Response({'received': True})


class PaymentConfigView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        keys = (
            'PAYNOW_INTEGRATION_ID',
            'PAYNOW_INTEGRATION_KEY',
            'PAYNOW_AUTH_EMAIL',
            'PAYNOW_RETURN_URL',
            'PAYNOW_RESULT_URL',
        )
        values = {key: bool(env(key)) for key in keys}
        email = env('PAYNOW_AUTH_EMAIL')
        at_sign = email.find('@')
        values['PAYNOW_AUTH_EMAIL_MASKED'] = (
            f'{email[:1]}{"*" * max(0, at_sign - 2)}{email[at_sign - 1:]}' if at_sign > -1 else ''
        )
        return Response(values)
