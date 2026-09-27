"""Configuration bridge between Django settings and the AsiaPay client.

A project needs the six ``ASIA_PAY_*`` credentials and
``ASIA_PAY_RECEIPT_MODEL``, the model that records its payments. The receipt's
field names (``ASIA_PAY_RECEIPT_REF_FIELD`` / ``_STATUS_FIELD``) default to
``ref_no`` / ``is_completed``.
"""

from dataclasses import dataclass, field

from django.conf import settings

from .client import AsiaPayClient


@dataclass
class AsiaPayConf:
    base_url: str
    app_key: str
    app_secret: str
    private_key: str
    app_id: str
    merchant_code: str
    currency: str = 'IQD'
    timeout: int = 30
    timeout_express: str = '30m'
    # starts every merch_order_id: <prefix><receipt pk>T<unix time>
    order_prefix: str = ''
    notify_url: str = ''
    redirect_url: str = ''
    # receipt binding
    receipt_model: str = ''
    ref_field: str = 'ref_no'
    status_field: str = 'is_completed'
    # optional dotted path to a callable(receipt, status_value)
    on_completed: str = ''
    _client: AsiaPayClient = field(default=None, repr=False, compare=False)


_conf = None


def get_conf():
    """Build (once) and return the AsiaPay configuration from Django settings."""
    global _conf
    if _conf is None:
        _conf = AsiaPayConf(
            base_url=getattr(settings, 'ASIA_PAY_BASE_URL', ''),
            app_key=getattr(settings, 'ASIA_PAY_APP_KEY', ''),
            app_secret=getattr(settings, 'ASIA_PAY_APP_SECRET', ''),
            private_key=getattr(settings, 'ASIA_PAY_JWT_PRIVATE_KEY', ''),
            app_id=getattr(settings, 'ASIA_PAY_APP_ID', ''),
            merchant_code=getattr(settings, 'ASIA_PAY_MERCHANT_CODE', ''),
            currency=getattr(settings, 'ASIA_PAY_CURRENCY', 'IQD'),
            timeout=int(getattr(settings, 'ASIA_PAY_TIMEOUT', 30)),
            timeout_express=getattr(settings, 'ASIA_PAY_TIMEOUT_EXPRESS', '30m'),
            order_prefix=getattr(settings, 'ASIA_PAY_ORDER_PREFIX', ''),
            notify_url=getattr(settings, 'ASIA_PAY_NOTIFY_URL', ''),
            redirect_url=getattr(settings, 'ASIA_PAY_REDIRECT_URL', ''),
            receipt_model=getattr(settings, 'ASIA_PAY_RECEIPT_MODEL', ''),
            ref_field=getattr(settings, 'ASIA_PAY_RECEIPT_REF_FIELD', 'ref_no'),
            status_field=getattr(
                settings, 'ASIA_PAY_RECEIPT_STATUS_FIELD', 'is_completed'
            ),
            on_completed=getattr(settings, 'ASIA_PAY_ON_PAYMENT_COMPLETED', ''),
        )
    return _conf


def get_client():
    """Return a process-wide AsiaPay client (token cache is shared)."""
    conf = get_conf()
    if conf._client is None:
        conf._client = AsiaPayClient(
            base_url=conf.base_url,
            app_key=conf.app_key,
            app_secret=conf.app_secret,
            private_key=conf.private_key,
            app_id=conf.app_id,
            merchant_code=conf.merchant_code,
            currency=conf.currency,
            timeout=conf.timeout,
        )
    return conf._client


def reset():
    """Drop cached config/client. Useful in tests and after settings override."""
    global _conf
    _conf = None
