from django.contrib import admin
from .models import Category, Product, Review, WishlistItem

admin.site.register([Category, Product, Review, WishlistItem])
