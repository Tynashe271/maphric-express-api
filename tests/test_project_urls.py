import json

from django.test import TestCase


class ProjectUrlTests(TestCase):
    def test_health_endpoint_reports_ok(self):
        response = self.client.get('/health/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content.decode()), {'status': 'ok'})

    def test_api_roots_are_routed(self):
        for path in ('/api/v1/products/products/', '/api/v1/products/categories/'):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_schema_is_publicly_available(self):
        response = self.client.get('/swagger/?format=openapi')

        self.assertEqual(response.status_code, 200)
        self.assertIn('paths', response.json())
