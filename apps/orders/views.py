from django.db import transaction
from django.db.models import F, Sum
from django.http import FileResponse
from django.core.serializers.json import DjangoJSONEncoder
from io import BytesIO
import json
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.cart.models import CartItem
from .models import Order, OrderItem, TransactionArchive
from .serializers import OrderSerializer


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Order.objects.filter(user=self.request.user).prefetch_related('items')
        return Order.objects.prefetch_related('items') if self.request.user.is_staff else queryset

    @action(detail=False, methods=['post'])
    def checkout(self, request):
        serializer = OrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart_items = list(CartItem.objects.filter(user=request.user).select_related('product'))
        if not cart_items:
            return Response({'detail': 'Your cart is empty.'}, status=status.HTTP_400_BAD_REQUEST)
        if any(item.quantity > item.product.stock_quantity for item in cart_items):
            return Response({'detail': 'One or more products are out of stock.'}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            total = sum(item.subtotal for item in cart_items)
            order = Order.objects.create(user=request.user, total=total, **serializer.validated_data)
            for item in cart_items:
                OrderItem.objects.create(order=order, product=item.product, product_name=item.product.name, unit_price=item.product.price, quantity=item.quantity)
                item.product.stock_quantity -= item.quantity
                item.product.save(update_fields=['stock_quantity'])
            CartItem.objects.filter(user=request.user).delete()
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAdminUser], url_path='wipe-transactions')
    def wipe_transactions(self, request):
        if request.data.get('confirmation') != 'WIPE ALL TRANSACTIONS':
            return Response(
                {'detail': 'Type WIPE ALL TRANSACTIONS to confirm.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            orders = Order.objects.all()
            count = orders.count()
            snapshot = json.loads(json.dumps(OrderSerializer(orders, many=True).data, cls=DjangoJSONEncoder))
            total = orders.aggregate(value=Sum('total'))['value'] or 0
            archive = TransactionArchive.objects.create(
                created_by=request.user,
                transaction_count=count,
                total_amount=total,
                data=snapshot,
            )
            for item in OrderItem.objects.filter(order__in=orders):
                item.product.__class__.objects.filter(pk=item.product_id).update(
                    stock_quantity=F('stock_quantity') + item.quantity
                )
            orders.delete()
        return Response({
            'deleted': count,
            'archive_id': archive.id,
            'detail': 'Transactions were archived and removed from the active list.',
        })

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAdminUser], url_path='transaction-archives')
    def transaction_archives(self, request):
        archives = TransactionArchive.objects.all()
        return Response([
            {
                'id': archive.id,
                'transaction_count': archive.transaction_count,
                'total_amount': archive.total_amount,
                'created_at': archive.created_at,
            }
            for archive in archives
        ])

    @action(
        detail=False,
        methods=['get'],
        permission_classes=[permissions.IsAdminUser],
        url_path=r'transaction-archives/(?P<archive_id>\d+)/pdf',
    )
    def transaction_archive_pdf(self, request, archive_id=None):
        archive = TransactionArchive.objects.filter(pk=archive_id).first()
        if not archive:
            return Response({'detail': 'Archive not found.'}, status=status.HTTP_404_NOT_FOUND)
        output = BytesIO()
        pdf = canvas.Canvas(output, pagesize=A4)
        width, height = A4
        y = height - 48

        def line(text, size=10, bold=False):
            nonlocal y
            if y < 55:
                pdf.showPage()
                y = height - 48
            pdf.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
            pdf.drawString(44, y, str(text)[:105])
            y -= size + 7

        line('MAPHRIC INVESTMENTS T/A ENGEN BRADFIELD EXPRESS SHOP', 13, True)
        line('Archived Transaction Report', 16, True)
        line(f'Archive #{archive.id} | Created: {archive.created_at:%d %b %Y %H:%M}')
        line(f'Transactions: {archive.transaction_count} | Total value: ${archive.total_amount}', 11, True)
        y -= 8
        for order in archive.data:
            line(f"MAP-{int(order['id']):06d} | {order.get('status', '').upper()} | ${order.get('total', '0.00')}", 11, True)
            line(f"Customer: {order.get('shipping_name', '')} | Phone: {order.get('shipping_phone', '')}")
            line(f"Payment: {order.get('payment_method') or 'Not selected'} | {order.get('payment_status', 'unpaid')}")
            for item in order.get('items', []):
                line(f"  {item.get('product_name', '')} x {item.get('quantity', 0)} @ ${item.get('unit_price', '0.00')}", 9)
            y -= 8
        pdf.save()
        output.seek(0)
        return FileResponse(output, as_attachment=True, filename=f'maphric-transactions-archive-{archive.id}.pdf')
