from rest_framework import serializers
from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'product_name', 'unit_price', 'quantity']


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = ['id', 'status', 'total', 'shipping_name', 'shipping_phone', 'shipping_address',
                  'payment_method', 'payment_status', 'items', 'created_at', 'updated_at']
        read_only_fields = ['id', 'status', 'total', 'payment_method', 'payment_status', 'items', 'created_at', 'updated_at']
