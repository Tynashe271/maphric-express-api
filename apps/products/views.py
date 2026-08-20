from decimal import Decimal, InvalidOperation
from django.db.models import Q
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from .models import Category, Product, Review, WishlistItem
from .serializers import CategorySerializer, ProductSerializer, ReviewSerializer, WishlistItemSerializer


def decimal_param(params, name):
    """Return a validated decimal query parameter, or None when absent."""
    raw = params.get(name)
    if raw in (None, ''):
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({name: 'Enter a valid amount.'})
    if value < 0:
        raise ValidationError({name: 'Enter an amount of 0 or more.'})
    return value


class IsAdminOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.method in permissions.SAFE_METHODS or bool(request.user and request.user.is_staff)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAdminOrReadOnly]


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = [IsAdminOrReadOnly]
    lookup_field = 'slug'

    def get_queryset(self):
        queryset = Product.objects.select_related('category').filter(is_active=True)
        params = self.request.query_params
        if self.request.user.is_authenticated and self.request.user.is_staff:
            queryset = Product.objects.select_related('category').all()
        if category := params.get('category'):
            queryset = queryset.filter(category__slug=category)
        if brand := params.get('brand'):
            queryset = queryset.filter(brand__iexact=brand)
        if search := params.get('search'):
            queryset = queryset.filter(Q(name__icontains=search[:100]) | Q(description__icontains=search[:100]))
        if (min_price := decimal_param(params, 'min_price')) is not None:
            queryset = queryset.filter(price__gte=min_price)
        if (max_price := decimal_param(params, 'max_price')) is not None:
            queryset = queryset.filter(price__lte=max_price)
        allowed_sorting = {'price', '-price', 'created_at', '-created_at'}
        if ordering := params.get('ordering'):
            if ordering in allowed_sorting:
                queryset = queryset.order_by(ordering)
        return queryset

    @action(detail=False, methods=['get'])
    def featured(self, request):
        return Response(self.get_serializer(self.get_queryset().filter(is_featured=True), many=True).data)

    @action(detail=False, methods=['get'])
    def recent(self, request):
        return Response(self.get_serializer(self.get_queryset().order_by('-created_at')[:12], many=True).data)

    @action(detail=True, methods=['get', 'post'], permission_classes=[permissions.IsAuthenticatedOrReadOnly])
    def reviews(self, request, slug=None):
        product = self.get_object()
        if request.method == 'GET':
            return Response(ReviewSerializer(product.reviews.select_related('user'), many=True).data)
        serializer = ReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review, _ = Review.objects.update_or_create(product=product, user=request.user, defaults=serializer.validated_data)
        return Response(ReviewSerializer(review).data)


class WishlistViewSet(viewsets.ModelViewSet):
    serializer_class = WishlistItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return WishlistItem.objects.filter(user=self.request.user).select_related('product', 'product__category')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
