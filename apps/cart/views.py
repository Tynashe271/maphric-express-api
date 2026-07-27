from rest_framework import permissions, viewsets
from .models import CartItem
from .serializers import CartItemSerializer


class CartViewSet(viewsets.ModelViewSet):
    serializer_class = CartItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return CartItem.objects.filter(user=self.request.user).select_related('product', 'product__category')

    def perform_create(self, serializer):
        item, created = CartItem.objects.get_or_create(user=self.request.user, product=serializer.validated_data['product'], defaults={'quantity': serializer.validated_data.get('quantity', 1)})
        if not created:
            item.quantity += serializer.validated_data.get('quantity', 1)
            item.save(update_fields=['quantity', 'updated_at'])
        serializer.instance = item
