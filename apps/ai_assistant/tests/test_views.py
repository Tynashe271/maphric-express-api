import json
from unittest import mock
from urllib import error

from django.test import TestCase
from rest_framework.test import APIClient

from apps.factories import create_order, create_product, create_user

CHAT_URL = '/api/v1/ai/chat/'


def openai_response(payload):
    api_response = mock.MagicMock()
    api_response.read.return_value = json.dumps(payload).encode()
    context = mock.MagicMock()
    context.__enter__.return_value = api_response
    return context


class ShoppingAssistantViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.product = create_product(name='Maize Meal', price='4.50', stock_quantity=10)
        self.client.force_authenticate(self.user)

    def _post(self, payload, api_result=None, side_effect=None):
        target = 'apps.ai_assistant.views.request.urlopen'
        if side_effect is not None:
            with mock.patch(target, side_effect=side_effect) as urlopen:
                response = self.client.post(CHAT_URL, payload, format='json')
        else:
            with mock.patch(target, return_value=openai_response(api_result or {})) as urlopen:
                response = self.client.post(CHAT_URL, payload, format='json')
        self.urlopen = urlopen
        return response

    def test_authentication_is_required(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.post(CHAT_URL, {'message': 'hi'}, format='json').status_code, 401)

    def test_empty_message_is_rejected(self):
        response = self.client.post(CHAT_URL, {'message': '   '}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_overlong_message_is_rejected(self):
        response = self.client.post(CHAT_URL, {'message': 'a' * 1201}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_answer_from_output_text_is_returned(self):
        response = self._post({'message': 'Do you sell maize meal?'}, {'output_text': 'Yes, we do.'})

        self.assertEqual(response.data, {'answer': 'Yes, we do.'})

    def test_answer_is_assembled_from_output_content(self):
        api_result = {
            'output': [
                {'content': [{'type': 'output_text', 'text': 'Yes,'}, {'type': 'refusal', 'text': 'ignored'}]},
                {'content': [{'type': 'output_text', 'text': 'we do.'}]},
            ]
        }

        response = self._post({'message': 'Do you sell maize meal?'}, api_result)

        self.assertEqual(response.data, {'answer': 'Yes,\nwe do.'})

    def test_empty_answer_returns_bad_gateway(self):
        response = self._post({'message': 'Do you sell maize meal?'}, {'output': []})

        self.assertEqual(response.status_code, 502)

    def test_provider_http_error_falls_back_to_the_catalogue(self):
        http_error = error.HTTPError('url', 500, 'Server Error', {}, None)

        response = self._post({'message': 'Do you sell maize meal?'}, side_effect=http_error)

        self.assertEqual(response.data['mode'], 'catalogue')
        self.assertIn('Maize Meal', response.data['answer'])

    def test_provider_connection_error_falls_back_to_the_catalogue(self):
        response = self._post({'message': 'Do you sell maize meal?'}, side_effect=error.URLError('offline'))

        self.assertEqual(response.data['mode'], 'catalogue')

    def test_invalid_provider_payload_falls_back_to_the_catalogue(self):
        api_response = mock.MagicMock()
        api_response.read.return_value = b'not json'
        context = mock.MagicMock()
        context.__enter__.return_value = api_response

        with mock.patch('apps.ai_assistant.views.request.urlopen', return_value=context):
            response = self.client.post(CHAT_URL, {'message': 'stock?'}, format='json')

        self.assertEqual(response.data['mode'], 'catalogue')

    def test_prompt_includes_catalogue_orders_and_valid_history_only(self):
        order = create_order(self.user, total='9.00')
        history = [
            {'role': 'system', 'content': 'dropped'},
            {'role': 'user', 'content': ''},
            {'role': 'assistant', 'content': 'Earlier answer'},
        ]

        self._post({'message': 'And the price?', 'history': history}, {'output_text': 'USD 4.50'})

        sent = json.loads(self.urlopen.call_args.args[0].data.decode())
        self.assertEqual(sent['input'], [
            {'role': 'assistant', 'content': 'Earlier answer'},
            {'role': 'user', 'content': 'And the price?'},
        ])
        self.assertIn('Maize Meal | Groceries | USD 4.50 | stock 10', sent['instructions'])
        self.assertIn(f'HHB-{order.id:06d}', sent['instructions'])

    def test_prompt_describes_an_empty_store(self):
        self.product.delete()

        self._post({'message': 'Anything in stock?'}, {'output_text': 'Not yet.'})

        sent = json.loads(self.urlopen.call_args.args[0].data.decode())
        self.assertIn('No products have been added yet.', sent['instructions'])
        self.assertIn('No customer orders yet.', sent['instructions'])

    def test_history_is_limited_to_the_last_six_entries(self):
        history = [{'role': 'user', 'content': f'question {index}'} for index in range(10)]

        self._post({'message': 'latest', 'history': history}, {'output_text': 'ok'})

        sent = json.loads(self.urlopen.call_args.args[0].data.decode())
        self.assertEqual(len(sent['input']), 7)
        self.assertEqual(sent['input'][0]['content'], 'question 4')
