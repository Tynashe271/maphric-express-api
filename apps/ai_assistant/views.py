import difflib
import json
import re
from urllib import error, request

from django.conf import settings
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cart.models import CartItem
from apps.common.responses import bad_gateway, error_response
from apps.common.text import format_order_reference
from apps.products.models import Product

ADD_TO_CART_TOOL = {
    'type': 'function',
    'function': {
        'name': 'add_to_cart',
        'description': (
            'Add grocery items the customer wants to buy to their shopping cart. '
            'Call this as soon as the customer lists specific products (and, '
            'optionally, quantities) they want to purchase, even mid-conversation '
            "and even if they haven't finished shopping. Only use product names "
            'from the supplied catalogue.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'items': {
                    'type': 'array',
                    'description': "Products the customer wants added to their cart.",
                    'items': {
                        'type': 'object',
                        'properties': {
                            'product_name': {
                                'type': 'string',
                                'description': 'Catalogue product name the customer wants.',
                            },
                            'quantity': {
                                'type': 'integer',
                                'description': 'How many units. Defaults to 1 if not stated.',
                            },
                        },
                        'required': ['product_name', 'quantity'],
                    },
                },
            },
            'required': ['items'],
        },
    },
}


class ShoppingAssistantView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def match_product(query, products, by_name):
        """Resolve free-form text like 'brown bread' to a catalogue product."""
        product = by_name.get(query)
        if product is not None:
            return product
        close = difflib.get_close_matches(query, by_name.keys(), n=1, cutoff=0.5)
        if close:
            return by_name[close[0]]
        contains = [p for p in products if query in p.name.lower() or p.name.lower() in query]
        return contains[0] if contains else None

    @classmethod
    def add_items_to_cart(cls, user, requested_items, products):
        """Resolve requested items against the catalogue and add matches to
        the customer's cart, merging quantities into any existing line."""
        by_name = {p.name.lower(): p for p in products}
        added, unavailable = [], []
        for entry in list(requested_items)[:20]:
            raw_name = str(entry.get('product_name', '')).strip()
            if not raw_name:
                continue
            try:
                quantity = max(1, min(50, int(entry.get('quantity') or 1)))
            except (TypeError, ValueError):
                quantity = 1

            product = cls.match_product(raw_name.lower(), products, by_name)
            if product is None:
                unavailable.append({'requested': raw_name, 'reason': 'not found in the catalogue'})
                continue
            if product.stock_quantity <= 0:
                unavailable.append({'requested': product.name, 'reason': 'out of stock'})
                continue

            quantity = min(quantity, product.stock_quantity)
            item, created = CartItem.objects.get_or_create(
                user=user, product=product, defaults={'quantity': quantity},
            )
            if not created:
                item.quantity += quantity
                item.save(update_fields=['quantity', 'updated_at'])
            added.append({
                'product_id': product.id,
                'name': product.name,
                'quantity': quantity,
                'subtotal': str(item.subtotal),
            })
        return added, unavailable

    @staticmethod
    def cart_confirmation_message(added, unavailable):
        parts = []
        if added:
            listing = ', '.join(f"{item['quantity']} x {item['name']}" for item in added)
            parts.append(f"I've added {listing} to your cart.")
        if unavailable:
            listing = ', '.join(f"{item['requested']} ({item['reason']})" for item in unavailable)
            parts.append(f"I could not add: {listing}.")
        if added:
            parts.append('Taking you to your cart to review it and complete payment.')
        return ' '.join(parts) or 'I could not find any of those items in the current catalogue.'

    @staticmethod
    def parse_shopping_list(message, products):
        """Best-effort '<qty> <product>' extraction for when the AI provider
        is unreachable, so listing items to buy still works without it."""
        text = message.lower()
        if not any(word in text for word in ('cart', 'buy', 'want', 'purchase', 'get me')):
            return []
        requested = []
        for segment in re.split(r',| and ', text):
            segment = segment.strip()
            if not segment:
                continue
            match = re.search(r'\b(\d+)\b', segment)
            if match:
                quantity = int(match.group(1))
                name = (segment[:match.start()] + ' ' + segment[match.end():]).strip()
            else:
                quantity, name = 1, segment
            name = re.sub(r'^(?:a|an|some|of|to my cart|to cart)\b\s*', '', name.strip()).strip()
            name = re.sub(r'\b(?:to my cart|to the cart|to cart)$', '', name).strip()
            if name and any(name in p.name.lower() or p.name.lower() in name for p in products):
                requested.append({'product_name': name, 'quantity': quantity})
        return requested

    @staticmethod
    def catalogue_fallback(message, products, recent_orders):
        """Keep essential shopping help available if the AI provider is offline."""
        query = message.lower()
        available = [p for p in products if p.stock_quantity > 0]

        if any(word in query for word in ('delivery', 'deliver', 'shipping', 'arrive')):
            return (
                'HarvestHub offers local delivery from Bradfield across Bulawayo. '
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
        history = request_obj.data.get('history', [])
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

        system_message = {
            'role': 'system',
            'content': (
                'You are the HarvestHub customer shopping assistant for the Bradfield, '
                'Bulawayo grocery shop. Answer customer queries, shopping requests, product '
                'questions, order questions, delivery questions, and store questions. Use only '
                'the supplied catalogue and order data for availability, price, stock, or order '
                'status. Never invent products, prices, payment confirmation, delivery status, '
                'or policies. All catalogue prices are USD. Be friendly and concise. If human '
                'help is needed, direct the customer to WhatsApp or phone +263 77 291 0496. '
                'Do not claim you placed an order or changed an account. When the customer '
                "lists items they want to buy, call add_to_cart with those items instead of "
                'just describing them in text.\n\n'
                f'CURRENT CATALOGUE:\n{catalogue}\n\nCUSTOMER ORDERS:\n{orders}'
            ),
        }
        messages = [system_message]
        for item in history[-6:]:
            role = item.get('role')
            content = str(item.get('content', ''))[:800]
            if role in {'user', 'assistant'} and content:
                messages.append({'role': role, 'content': content})
        messages.append({'role': 'user', 'content': message})

        payload = {
            'model': settings.AI_MODEL,
            'messages': messages,
            'tools': [ADD_TO_CART_TOOL],
            'max_tokens': 500,
        }
        api_request = request.Request(
            f'{settings.AI_API_BASE_URL.rstrip("/")}/chat/completions',
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {settings.AI_API_KEY}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        try:
            with request.urlopen(api_request, timeout=35) as api_response:
                result = json.loads(api_response.read().decode('utf-8'))
        except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError):
            requested = self.parse_shopping_list(message, products)
            if requested:
                added, unavailable = self.add_items_to_cart(request_obj.user, requested, products)
                if added:
                    return Response({
                        'answer': self.cart_confirmation_message(added, unavailable),
                        'cart_items_added': added,
                        'unavailable_items': unavailable,
                        'redirect_to_cart': True,
                        'mode': 'catalogue',
                    })
            return Response({
                'answer': self.catalogue_fallback(message, products, recent_orders),
                'mode': 'catalogue',
            })

        choices = result.get('choices') or []
        response_message = choices[0].get('message', {}) if choices else {}
        tool_calls = response_message.get('tool_calls') or []
        tool_call = next(
            (call for call in tool_calls if call.get('function', {}).get('name') == 'add_to_cart'),
            None,
        )
        if tool_call is not None:
            try:
                arguments = json.loads(tool_call['function'].get('arguments') or '{}')
            except json.JSONDecodeError:
                arguments = {}
            added, unavailable = self.add_items_to_cart(
                request_obj.user, arguments.get('items') or [], products,
            )
            return Response({
                'answer': self.cart_confirmation_message(added, unavailable),
                'cart_items_added': added,
                'unavailable_items': unavailable,
                'redirect_to_cart': bool(added),
            })

        answer = (response_message.get('content') or '').strip()
        if not answer:
            return bad_gateway('The assistant returned no answer.')
        return Response({'answer': answer})
