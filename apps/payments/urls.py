from django.urls import path
from .views import InitiatePaymentView, PaymentCallbackView, PaymentConfigView, PaymentStatusView

urlpatterns = [
    path('initiate/', InitiatePaymentView.as_view(), name='payment-initiate'),
    path('status/<int:order_id>/', PaymentStatusView.as_view(), name='payment-status'),
    path('callback/', PaymentCallbackView.as_view(), name='payment-callback'),
    path('config/', PaymentConfigView.as_view(), name='payment-config'),
]
