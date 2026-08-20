"""Object builders shared by the test suite."""

from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.cart.models import CartItem
from apps.orders.models import Order, OrderItem
from apps.products.models import Category, Product

User = get_user_model()


def create_user(username='shopper', password='str0ng-passw0rd!', **extra):
    extra.setdefault('email', f'{username}@example.com')
    extra.setdefault('phone_number', '0771234567')
    return User.objects.create_user(username=username, password=password, **extra)


def create_admin(username='manager', password='str0ng-passw0rd!', **extra):
    extra.setdefault('phone_number', '0779999999')
    return create_user(username=username, password=password, is_staff=True, is_superuser=True, **extra)


def create_category(name='Groceries', **extra):
    extra.setdefault('slug', name.lower().replace(' ', '-'))
    return Category.objects.create(name=name, **extra)


def create_product(category=None, name='Maize Meal', price='4.50', stock_quantity=10, **extra):
    extra.setdefault('slug', name.lower().replace(' ', '-'))
    extra.setdefault('description', f'{name} description')
    return Product.objects.create(
        category=category or create_category(),
        name=name,
        price=Decimal(price),
        stock_quantity=stock_quantity,
        **extra,
    )


def create_cart_item(user, product, quantity=1):
    return CartItem.objects.create(user=user, product=product, quantity=quantity)


def create_order(user, total='10.00', **extra):
    extra.setdefault('shipping_name', 'Test Shopper')
    extra.setdefault('shipping_phone', '0771234567')
    extra.setdefault('shipping_address', '1 Bradfield Road, Bulawayo')
    return Order.objects.create(user=user, total=Decimal(total), **extra)


def create_order_item(order, product, quantity=1):
    return OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        unit_price=product.price,
        quantity=quantity,
    )
