from django.db import transaction
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.cart.models import CartItem
from .models import Order, OrderItem
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
