"""HTTP surface for AsiaPay: the ``notify_url`` it calls on a status change.

Starting a payment stays in the project (it has to create the receipt first),
so this app exposes only the callback, which is public and re-verified.
"""

import logging

import jwt
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from . import service
from .conf import get_client
from .exceptions import AsiaPayError

logger = logging.getLogger('asiapay')


def _merch_order_id(data):
    """Find the order id wherever AsiaPay puts it.

    The notification body is not documented, so accept the id flat, inside
    ``biz_content`` (the shape of every other AsiaPay message), or inside the
    signed ``sign`` JWT. It is only used to *look up* the order; the status is
    always read back from AsiaPay.
    """
    biz = data.get('biz_content')
    if isinstance(biz, dict) and biz.get('merch_order_id'):
        return biz['merch_order_id']
    if data.get('merch_order_id'):
        return data['merch_order_id']
    sign = data.get('sign')
    if sign:
        try:
            return get_client().verify(sign).get('merch_order_id')
        except (jwt.InvalidTokenError, AsiaPayError):
            return None
    return None


class AsiaPayCallbackView(APIView):
    """Public endpoint AsiaPay calls when an order's status changes."""

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def post(self, request):
        return self._handle(request.data)

    def get(self, request):
        return self._handle(request.query_params)

    def _handle(self, data):
        merch_order_id = _merch_order_id(data)
        if not merch_order_id:
            logger.warning('AsiaPay callback without an order id: %s', dict(data))
            return Response({'message': 'missing merch_order_id'}, status=400)

        try:
            service.handle_callback(merch_order_id)
        except AsiaPayError as exc:
            # ask AsiaPay to retry later rather than swallow a transient failure
            logger.error('AsiaPay callback processing failed: %s', exc)
            return Response({'message': str(exc)}, status=503)

        return Response({'result': 'SUCCESS', 'message': 'ok'})
