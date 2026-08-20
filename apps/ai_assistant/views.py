import json
import re
from urllib import error, request

from django.conf import settings
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.responses import bad_gateway, error_response
from apps.common.text import format_order_reference
from apps.products.models import Product


class ShoppingAssistantView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def catalogue_fallback(message, products, recent_orders):
        """Keep essential shopping help available if the AI provider is offline."""
        query = message.lower()
        available = [p for p in products if p.stock_quantity > 0]

        if any(word in query for word in ('delivery', 'deliver', 'shipping', 'arrive')):
            return (
                'Maphric Express offers local delivery from Bradfield across Bulawayo. '
                'After checkout, use your MAP order number on Track delivery to see the '
                'latest status and estimated arrival time.'
            )

        if any(word in query for word in ('order', 'track', 'status')):
            if not recent_orders:
                return 'You have no recent orders yet. Completed checkouts will appear in Order history.'
            order = recent_orders[0]
            return (
                f'Your latest order is {format_order_reference(order.id)}. Its current status is '
                f'{order.status} and its total is USD {order.total}.'
            )

        budget_match = re.search(r'(?:\$|usd\s*)?(\d+(?:\.\d{1,2})?)', query)
        if budget_match and any(word in query for word in ('buy', 'budget', 'afford', 'spend', '$', 'usd')):
            budget = float(budget_match.group(1))
            choices = sorted((p for p in available if float(p.price) <= budget), key=lambda p: p.price)
            if not choices:
                return f'There are currently no in-stock products priced at USD {budget:.2f} or less.'
            lines = ', '.join(f'{p.name} (USD {p.price})' for p in choices[:10])
            return f'For a USD {budget:.2f} budget, these in-stock options fit: {lines}. Prices are from the current shop catalogue.'

        matches = [
            p for p in products
            if p.name.lower() in query
            or p.category.name.lower() in query
            or any(term in p.name.lower() for term in query.split() if len(term) > 2)
        ]
        if matches:
            lines = ', '.join(
                f'{p.name} — USD {p.price} ({p.stock_quantity} in stock)'
                if p.stock_quantity else f'{p.name} — USD {p.price} (out of stock)'
                for p in matches[:12]
            )
            return f'Here is the current catalogue information: {lines}.'

        if any(word in query for word in ('stock', 'available', 'groceries', 'products', 'price')):
            if not available:
                return 'No groceries are currently marked as in stock. Please check again after the administrator adds inventory.'
            lines = ', '.join(f'{p.name} — USD {p.price}' for p in available[:12])
            extra = f' There are {len(available) - 12} more in-stock products.' if len(available) > 12 else ''
            return f'Currently in stock: {lines}.{extra}'

        return (
            'I can help with current products, prices, stock, shopping budgets, delivery, '
            'and your recent order status. Please ask a specific shopping question.'
        )

    def post(self, request_obj):
        message = str(request_obj.data.get('message', '')).strip()
        history = request_obj.data.get('history') or []
        if not isinstance(history, list):
            return error_response('Conversation history must be a list of messages.')
        if not message:
            return error_response('Please enter a question.')
        if len(message) > 1200:
            return error_response('Please shorten your question.')

        products = list(Product.objects.filter(is_active=True).select_related('category')[:80])
        catalogue = '\n'.join(
            f"- {p.name} | {p.category.name} | USD {p.price} | stock {p.stock_quantity}"
            for p in products
        ) or 'No products have been added yet.'
        recent_orders = list(request_obj.user.orders.order_by('-created_at')[:5])
        orders = '\n'.join(
            f"- {format_order_reference(o.id)}: {o.status}, total USD {o.total}"
            for o in recent_orders
        ) or 'No customer orders yet.'

        prior = []
        for item in history[-6:]:
            if not isinstance(item, dict):
                continue
            role = item.get('role')
            content = str(item.get('content', ''))[:800]
            if role in {'user', 'assistant'} and content:
                prior.append({'role': role, 'content': content})
        prior.append({'role': 'user', 'content': message})

        if not settings.OPENAI_API_KEY:
            return Response({
                'answer': self.catalogue_fallback(message, products, recent_orders),
                'mode': 'catalogue',
            })

        payload = {
            'model': settings.OPENAI_MODEL,
            'instructions': (
                'You are the Maphric Express customer shopping assistant for the Bradfield, '
                'Bulawayo grocery shop. Answer customer queries, shopping requests, product '
                'questions, order questions, delivery questions, and store questions. Use only '
                'the supplied catalogue and order data for availability, price, stock, or order '
                'status. Never invent products, prices, payment confirmation, delivery status, '
                'or policies. All catalogue prices are USD. Be friendly and concise. If human '
                'help is needed, direct the customer to WhatsApp or phone +263 77 291 0496. '
                'Do not claim you placed an order or changed an account.\n\n'
                f'CURRENT CATALOGUE:\n{catalogue}\n\nCUSTOMER ORDERS:\n{orders}'
            ),
            'input': prior,
            'max_output_tokens': 500,
        }
        api_request = request.Request(
            'https://api.openai.com/v1/responses',
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {settings.OPENAI_API_KEY}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        try:
            with request.urlopen(api_request, timeout=35) as api_response:
                result = json.loads(api_response.read().decode('utf-8'))
        except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError):
            return Response({
                'answer': self.catalogue_fallback(message, products, recent_orders),
                'mode': 'catalogue',
            })

        answer = result.get('output_text')
        if not answer:
            texts = []
            for output in result.get('output', []):
                for content in output.get('content', []):
                    if content.get('type') == 'output_text':
                        texts.append(content.get('text', ''))
            answer = '\n'.join(texts).strip()
        if not answer:
            return bad_gateway('The assistant returned no answer.')
        return Response({'answer': answer})
