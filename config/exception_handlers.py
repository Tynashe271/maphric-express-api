"""Project-wide DRF exception handling."""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger('maphric.api')


def _request_context(context):
    request = context.get('request')
    view = context.get('view')
    return {
        'method': getattr(request, 'method', ''),
        'path': getattr(request, 'path', ''),
        'view': type(view).__name__ if view else '',
    }


def api_exception_handler(exc, context):
    """Return a JSON error body for every failure and log server-side faults.

    Django ``ValidationError`` raised by model ``full_clean()`` or by query
    parameter coercion is reported as a 400 instead of an opaque 500, database
    faults become a 503, and anything left unhandled is logged with request
    context before Django's default 500 handling takes over.
    """
    if isinstance(exc, DjangoValidationError):
        exc = DRFValidationError(detail=list(exc.messages))

    response = drf_exception_handler(exc, context)
    details = _request_context(context)

    if response is None:
        if isinstance(exc, DatabaseError):
            logger.exception(
                'Database error while handling %(method)s %(path)s in %(view)s', details
            )
            return Response(
                {'detail': 'The service is temporarily unavailable. Please try again.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        logger.exception('Unhandled error while handling %(method)s %(path)s in %(view)s', details)
        return None

    if response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.error(
            'Server error %(status)s for %(method)s %(path)s in %(view)s',
            {**details, 'status': response.status_code},
            exc_info=exc,
        )
    return response
