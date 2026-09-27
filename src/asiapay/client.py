"""Standalone client for the AsiaPay payment gateway.

This module has **no Django dependency** on purpose: it only needs ``requests``
and ``PyJWT``, so it can be used on its own, driven by plain config values.

Contract (docs: https://asiapay.iq/integration, verified against the sandbox)::

    POST {base_url}/payment/gateway/payment/v1/token               -> token
    POST {base_url}/payment/gateway/payment/v1/merchant/preOrder   -> create order
    POST {base_url}/payment/gateway/payment/v1/merchant/queryOrder -> status
    POST {base_url}/payment/gateway/payment/v1/merchant/refund     -> refund

* Every call carries ``X-APP-Key``; all but the token call carry the token
  exactly as AsiaPay returns it (it already reads ``Bearer <token>``).
* Every body is signed: the whole body, ``sign`` excluded, is encoded as an
  HS256 JWT keyed with the merchant's private key *string* as given (not
  base64-decoded -- the sandbox refuses that) and sent back as ``sign``.
* A refusal can come back with a non-error HTTP status (the sandbox answers an
  unknown order with ``299``), so success is ``result == "SUCCESS"``, never the
  status code alone.

Trade types (per AsiaPay): ``Checkout`` for web, ``Cross-App`` for mobile apps
(opens the AsiaPay app), ``PWA`` for a static QR.

Order status values seen on ``queryOrder``: ``WAIT_PAY``, ``PAY_SUCCESS``, ...
"""

import threading
import time
import uuid
from datetime import datetime

import jwt
import requests

from .exceptions import AsiaPayAPIError, AsiaPayAuthError

TRADE_CHECKOUT = 'Checkout'
TRADE_CROSS_APP = 'Cross-App'
TRADE_PWA = 'PWA'
TRADE_TYPES = (TRADE_CHECKOUT, TRADE_CROSS_APP, TRADE_PWA)

PAY_SUCCESS = 'PAY_SUCCESS'

DEFAULT_CURRENCY = 'IQD'
DEFAULT_TIMEOUT = 30
# refresh a little before the token actually expires
TOKEN_EXPIRY_SKEW = 60
_API = '/payment/gateway/payment/v1'


class AsiaPayClient:
    """Thin, reusable wrapper around the AsiaPay merchant endpoints.

    The access token is cached in-memory for the lifetime AsiaPay grants it
    (and refreshed once more on a ``401``).
    """

    def __init__(
        self,
        base_url,
        app_key,
        app_secret,
        private_key,
        app_id,
        merchant_code,
        *,
        currency=DEFAULT_CURRENCY,
        timeout=DEFAULT_TIMEOUT,
    ):
        if not all((base_url, app_key, app_secret, private_key, app_id, merchant_code)):
            raise AsiaPayAuthError(
                'AsiaPay client requires base_url, app_key, app_secret, '
                'private_key, app_id and merchant_code.'
            )
        self.base_url = base_url.rstrip('/')
        self.app_key = app_key
        self.app_secret = app_secret
        self.private_key = private_key
        self.app_id = app_id
        self.merchant_code = merchant_code
        self.currency = currency
        self.timeout = timeout

        self._token = None
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    # -- signing ------------------------------------------------------------
    def sign(self, body):
        return jwt.encode(body, self.private_key, algorithm='HS256')

    def verify(self, sign):
        """Decode a ``sign`` AsiaPay sent us; raises ``jwt.InvalidTokenError``."""
        return jwt.decode(sign, self.private_key, algorithms=['HS256'])

    # -- auth ---------------------------------------------------------------
    def _fetch_token(self):
        try:
            resp = requests.post(
                f'{self.base_url}{_API}/token',
                json={'appSecret': self.app_secret},
                headers={'X-APP-Key': self.app_key},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AsiaPayAuthError(f'AsiaPay token request failed: {exc}') from exc

        data = _safe_json(resp)
        token = data.get('token')
        if resp.status_code != 200 or not token:
            raise AsiaPayAuthError(
                f'AsiaPay token request returned {resp.status_code}: {resp.text}'
            )

        self._token = token
        self._token_expiry = time.time() + _lifetime(
            data.get('effectiveDate'), data.get('expirationDate')
        )
        return token

    def _get_token(self, force=False):
        with self._lock:
            valid = self._token and time.time() < self._token_expiry - TOKEN_EXPIRY_SKEW
            if force or not valid:
                return self._fetch_token()
            return self._token

    # -- requests -----------------------------------------------------------
    def _request(self, path, method, biz_content):
        body = {
            'biz_content': biz_content,
            'method': method,
            'nonce_str': uuid.uuid4().hex,
            'sign_type': 'JWTSecret',
            'timestamp': str(int(time.time())),
            'version': '1.0',
        }
        body['sign'] = self.sign(body)
        url = f'{self.base_url}{_API}{path}'

        def send(force_token=False):
            headers = {
                'X-APP-Key': self.app_key,
                'Authorization': self._get_token(force=force_token),
            }
            return requests.post(url, json=body, headers=headers, timeout=self.timeout)

        try:
            resp = send()
            # token may have expired between calls -> refresh once and retry
            if resp.status_code == 401:
                resp = send(force_token=True)
        except requests.RequestException as exc:
            raise AsiaPayAPIError(f'AsiaPay request to {url} failed: {exc}') from exc

        data = _safe_json(resp)
        if resp.status_code >= 400 or data.get('result') != 'SUCCESS':
            message = data.get('msg') or data.get('errorMsg') or resp.text
            raise AsiaPayAPIError(
                f'AsiaPay {method} returned {resp.status_code}: {message}',
                status_code=resp.status_code,
                payload=data,
            )
        return data.get('biz_content') or {}

    # -- public api ---------------------------------------------------------
    def create_order(
        self,
        merch_order_id,
        amount,
        *,
        title,
        notify_url,
        redirect_url,
        trade_type=TRADE_CHECKOUT,
        timeout_express='30m',
    ):
        """Create an order; returns ``biz_content``.

        Keys: ``merch_order_id``, ``prepay_id`` and ``redirect_url`` -- the
        page (Checkout / PWA) or the app hand-off (Cross-App) to send the
        customer to.
        """
        return self._request(
            '/merchant/preOrder',
            'payment.preorder',
            {
                'appid': self.app_id,
                'business_type': 'BuyGoods',
                'merch_code': self.merchant_code,
                'merch_order_id': merch_order_id,
                'redirect_url': redirect_url,
                'notify_url': notify_url,
                'timeout_express': timeout_express,
                'title': title,
                'total_amount': _format_amount(amount),
                'trade_type': trade_type,
                'trans_currency': self.currency,
            },
        )

    def query_order(self, merch_order_id):
        """Return the order's ``biz_content`` (``order_status`` holds the state)."""
        return self._request(
            '/merchant/queryOrder',
            'payment.queryorder',
            {
                'appid': self.app_id,
                'merch_code': self.merchant_code,
                'merch_order_id': merch_order_id,
            },
        )

    def refund(self, merch_order_id, refund_request_no, reason):
        """Refund a paid order in full; ``refund_status`` holds the outcome."""
        return self._request(
            '/merchant/refund',
            'payment.refund',
            {
                'appid': self.app_id,
                'merch_code': self.merchant_code,
                'merch_order_id': merch_order_id,
                'refund_request_no': refund_request_no,
                'refund_reason': reason,
            },
        )


def _format_amount(amount):
    """IQD carries no minor units; AsiaPay takes the amount as a string."""
    return str(round(float(amount)))


def _lifetime(effective_date, expiration_date):
    """Seconds the token lives, from its ``YYYYmmddHHMMSS`` bounds.

    Only the difference is used: the dates carry no zone, so reading either
    one against our own clock could be hours off. Falls back to 10 minutes.
    """
    fmt = '%Y%m%d%H%M%S'
    try:
        start = datetime.strptime(effective_date, fmt)  # noqa: DTZ007
        end = datetime.strptime(expiration_date, fmt)  # noqa: DTZ007
    except (TypeError, ValueError):
        return 600
    return max((end - start).total_seconds(), 0)


def _safe_json(resp):
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {'raw': resp.text}
