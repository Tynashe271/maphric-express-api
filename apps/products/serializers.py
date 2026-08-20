from rest_framework import serializers
from .models import Category, Product, Review, WishlistItem


class ActiveProductField(serializers.PrimaryKeyRelatedField):
    """Write-only reference to a product that is still on sale."""

    def __init__(self, **kwargs):
        kwargs.setdefault('source', 'product')
        kwargs.setdefault('queryset', Product.objects.filter(is_active=True))
        kwargs.setdefault('write_only', True)
        super().__init__(**kwargs)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'


class ReviewSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = Review
        fields = ['id', 'user', 'rating', 'comment', 'created_at']
        read_only_fields = ['id', 'user', 'created_at']


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    average_rating = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ['id', 'category', 'category_name', 'name', 'slug', 'description', 'brand', 'price',
                  'stock_quantity', 'image', 'is_featured', 'is_active', 'average_rating', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_average_rating(self, product):
        reviews = list(product.reviews.values_list('rating', flat=True))
        return round(sum(reviews) / len(reviews), 1) if reviews else None


class WishlistItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)
    product_id = ActiveProductField()

    class Meta:
        model = WishlistItem
        fields = ['id', 'product', 'product_id', 'created_at']
        read_only_fields = ['id', 'created_at']
