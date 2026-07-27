from django.urls import path
from .views import ShoppingAssistantView

urlpatterns = [
    path('chat/', ShoppingAssistantView.as_view(), name='shopping-assistant'),
]
