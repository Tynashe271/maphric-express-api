from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import CartViewSet

router = DefaultRouter()
router.register('items', CartViewSet, basename='cart')
urlpatterns = [path('', include(router.urls))]
