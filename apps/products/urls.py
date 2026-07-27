from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import CategoryViewSet, ProductViewSet, WishlistViewSet

router = DefaultRouter()
router.register('categories', CategoryViewSet)
router.register('products', ProductViewSet, basename='product')
router.register('wishlist', WishlistViewSet, basename='wishlist')

urlpatterns = [path('', include(router.urls))]
