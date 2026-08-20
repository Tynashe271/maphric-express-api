"""Shared response builders so error payloads keep one shape."""

from rest_framework import status
from rest_framework.response import Response


def error_response(detail, status_code=status.HTTP_400_BAD_REQUEST):
    """Return the API's standard ``{'detail': ...}`` error body."""
    return Response({'detail': detail}, status=status_code)


def not_found(detail):
    return error_response(detail, status.HTTP_404_NOT_FOUND)


def service_unavailable(detail):
    return error_response(detail, status.HTTP_503_SERVICE_UNAVAILABLE)


def bad_gateway(detail):
    return error_response(detail, status.HTTP_502_BAD_GATEWAY)
