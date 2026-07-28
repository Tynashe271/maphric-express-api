import os
from paynow import Paynow
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.orders.models import Order


def gateway():
    integration_id = os.getenv('PAYNOW_INTEGRATION_ID', '').strip()
    integration_key = os.getenv('PAYNOW_INTEGRATION_KEY', '').strip()
    if not integration_id or not integration_key:
        raise RuntimeError('EcoCash merchant payments are not configured.')
    return Paynow(
        integration_id,
        integration_key,
        os.getenv('PAYNOW_RETURN_URL', ''),
        os.getenv('PAYNOW_RESULT_URL', ''),
    )


class InitiatePaymentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            return self._initiate(request)
        except Exception as exc:
            return Response(
                {'detail': f'EcoCash setup error: {type(exc).__name__}: {exc}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

    def _initiate(self, request):
        orders = Order.objects.all() if request.user.is_staff else Order.objects.filter(user=request.user)
        order = orders.filter(pk=request.data.get('order_id')).first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)
        if order.payment_status == 'paid':
            return Response({'paid': True, 'status': 'paid'})
        method = str(request.data.get('method', '')).lower()
        if method != 'ecocash':
            return Response({'detail': 'This endpoint currently accepts EcoCash payments.'}, status=status.HTTP_400_BAD_REQUEST)
        phone = ''.join(c for c in str(request.data.get('phone', '')) if c.isdigit() or c == '+')
        if not phone:
            return Response({'detail': 'Enter the EcoCash phone number.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            paynow = gateway()
            auth_email = (
                os.getenv('PAYNOW_AUTH_EMAIL', '')
                or order.user.email
                or request.user.email
                or ''
            ).strip()
            if not auth_email:
                return Response({'detail': 'The order account needs an email address before EcoCash payment.'}, status=status.HTTP_400_BAD_REQUEST)
            payment = paynow.create_payment(f'MAP-{order.id:06d}', auth_email)
            payment.add(f'Maphric Express order MAP-{order.id:06d}', float(order.total))
            response = paynow.send_mobile(payment, phone, 'ecocash')
        except Exception as exc:
            return Response({'detail': f'EcoCash could not be contacted: {exc}'}, status=status.HTTP_502_BAD_GATEWAY)
        if not getattr(response, 'success', False):
            response_data = getattr(response, 'data', {}) or {}
            paynow_error = response_data.get('error') if isinstance(response_data, dict) else None
            return Response(
                {'detail': str(paynow_error) if paynow_error else 'EcoCash rejected the payment request.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        poll_url = getattr(response, 'poll_url', '')
        if not poll_url:
            return Response({'detail': 'Paynow accepted the request but did not return a payment status URL.'}, status=status.HTTP_502_BAD_GATEWAY)
        order.payment_method = 'EcoCash'
        order.payment_status = 'pending'
        order.paynow_poll_url = poll_url
        order.save(update_fields=['payment_method', 'payment_status', 'paynow_poll_url', 'updated_at'])
        return Response({'paid': False, 'status': 'pending', 'message': 'Approve the EcoCash prompt on your phone.'})


class PaymentStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, order_id):
        order = Order.objects.filter(pk=order_id, user=request.user).first()
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)
        if order.payment_status == 'paid':
            return Response({'paid': True, 'status': 'paid'})
        if not order.paynow_poll_url:
            return Response({'paid': False, 'status': order.payment_status})
        try:
            payment_status = gateway().check_transaction_status(order.paynow_poll_url)
        except Exception as exc:
            return Response({'detail': f'Payment status could not be checked: {exc}'}, status=status.HTTP_502_BAD_GATEWAY)
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
        values = {key: bool(os.getenv(key, '').strip()) for key in keys}
        email = os.getenv('PAYNOW_AUTH_EMAIL', '').strip()
        values['PAYNOW_AUTH_EMAIL_MASKED'] = (
            f'{email[:1]}{"*" * max(0, email.find("@") - 2)}{email[email.find("@") - 1:]}'
            if '@' in email else ''
        )
        return Response(values)
