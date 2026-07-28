from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.http import FileResponse, HttpResponse, JsonResponse
from django.core.serializers.json import DjangoJSONEncoder
from io import BytesIO
import json
import csv
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.cart.models import CartItem
from apps.products.models import Product, Review
from .models import AdminActivity, Order, OrderItem, TransactionArchive
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
            AdminActivity.objects.create(
                actor=request.user,
                action='transactions_archived',
                description=f'Archived and removed {count} transactions.',
                metadata={'archive_id': archive.id, 'transaction_count': count, 'total_amount': str(total)},
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
        query = request.query_params.get('q', '').strip()
        start = request.query_params.get('from', '').strip()
        end = request.query_params.get('to', '').strip()
        if query.isdigit():
            archives = archives.filter(pk=int(query))
        if start:
            archives = archives.filter(created_at__date__gte=start)
        if end:
            archives = archives.filter(created_at__date__lte=end)
        return Response([
            {
                'id': archive.id,
                'transaction_count': archive.transaction_count,
                'total_amount': archive.total_amount,
                'created_at': archive.created_at,
                'created_by': archive.created_by.get_full_name() or archive.created_by.username if archive.created_by else 'System',
            }
            for archive in archives
        ])

    @action(
        detail=False,
        methods=['get'],
        permission_classes=[permissions.IsAdminUser],
        url_path=r'transaction-archives/(?P<archive_id>\d+)/csv',
    )
    def transaction_archive_csv(self, request, archive_id=None):
        archive = TransactionArchive.objects.filter(pk=archive_id).first()
        if not archive:
            return Response({'detail': 'Archive not found.'}, status=status.HTTP_404_NOT_FOUND)
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="maphric-transactions-archive-{archive.id}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Order number', 'Created', 'Customer', 'Phone', 'Status', 'Payment method', 'Payment status', 'Total'])
        for order in archive.data:
            writer.writerow([
                f"MAP-{int(order['id']):06d}", order.get('created_at', ''), order.get('shipping_name', ''),
                order.get('shipping_phone', ''), order.get('status', ''), order.get('payment_method', ''),
                order.get('payment_status', ''), order.get('total', '0.00'),
            ])
        return response

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAdminUser], url_path='admin-summary')
    def admin_summary(self, request):
        orders = Order.objects.all()
        totals = orders.aggregate(revenue=Sum('total'), transactions=Count('id'))
        payment_status = list(orders.values('payment_status').annotate(count=Count('id'), total=Sum('total')).order_by('payment_status'))
        order_status = list(orders.values('status').annotate(count=Count('id')).order_by('status'))
        low_stock = list(Product.objects.filter(is_active=True, stock_quantity__lte=8).values('id', 'name', 'stock_quantity', 'price').order_by('stock_quantity'))
        return Response({
            'revenue': totals['revenue'] or 0,
            'transactions': totals['transactions'] or 0,
            'payment_status': payment_status,
            'order_status': order_status,
            'low_stock': low_stock,
        })

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAdminUser], url_path='admin-activity')
    def admin_activity(self, request):
        return Response([
            {
                'id': item.id,
                'actor': item.actor.get_full_name() or item.actor.username if item.actor else 'System',
                'action': item.action,
                'description': item.description,
                'metadata': item.metadata,
                'created_at': item.created_at,
            }
            for item in AdminActivity.objects.select_related('actor')[:200]
        ])

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAdminUser], url_path='admin-reviews')
    def admin_reviews(self, request):
        return Response([
            {
                'id': review.id,
                'customer': review.user.get_full_name() or review.user.username,
                'product': review.product.name,
                'rating': review.rating,
                'comment': review.comment,
                'created_at': review.created_at,
            }
            for review in Review.objects.select_related('user', 'product')[:200]
        ])

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAdminUser], url_path='backup')
    def backup(self, request):
        payload = {
            'generated_at': request._request.META.get('REQUEST_TIME', ''),
            'orders': OrderSerializer(Order.objects.all(), many=True).data,
            'archives': [
                {
                    'id': archive.id,
                    'created_at': archive.created_at.isoformat(),
                    'transaction_count': archive.transaction_count,
                    'total_amount': str(archive.total_amount),
                    'data': archive.data,
                }
                for archive in TransactionArchive.objects.all()
            ],
            'products': list(Product.objects.all().values()),
        }
        AdminActivity.objects.create(actor=request.user, action='backup_downloaded', description='Downloaded a store data backup.')
        response = JsonResponse(payload, json_dumps_params={'indent': 2})
        response['Content-Disposition'] = 'attachment; filename="maphric-store-backup.json"'
        return response

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
