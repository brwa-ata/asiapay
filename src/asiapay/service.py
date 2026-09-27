"""Bridge between the AsiaPay client and a project's receipt model.

The receipt model needs a *reference* field to hold our ``merch_order_id``
(default ``ref_no``) and a boolean *status* field that stays ``False`` until
AsiaPay confirms the payment (default ``is_completed``). Neither is imported
directly -- see :mod:`asiapay.conf`.

Nothing AsiaPay *sends* us is trusted: the callback only names an order, and
the status is always read back through ``queryOrder`` before a receipt moves.
"""

import logging
import time

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils.module_loading import import_string

from .client import PAY_SUCCESS
from .conf import get_client, get_conf

logger = logging.getLogger('asiapay')


def get_receipt_model():
    model = get_conf().receipt_model
    if not model:
        raise ImproperlyConfigured(
            'Set ASIA_PAY_RECEIPT_MODEL to the model that records payments, '
            "e.g. 'shop.Receipt'."
        )
    return apps.get_model(model)


def start_payment(
    receipt, *, notify_url, redirect_url, trade_type, amount=None, title=None
):
    """Create an AsiaPay order for ``receipt`` and store its ``merch_order_id``.

    Marks the receipt pending (status field -> ``False``) and returns
    AsiaPay's ``biz_content`` (``merch_order_id``, ``prepay_id``,
    ``redirect_url``).
    """
    conf = get_conf()
    # the pk makes it unique here; the timestamp keeps it unique across the
    # databases (local, staging, live) that share one sandbox merchant
    merch_order_id = f'{conf.order_prefix}{receipt.pk}T{int(time.time())}'

    response = get_client().create_order(
        merch_order_id,
        amount if amount is not None else receipt.amount,
        title=title or _default_title(receipt),
        notify_url=notify_url,
        redirect_url=redirect_url,
        trade_type=trade_type,
        timeout_express=conf.timeout_express,
    )

    setattr(receipt, conf.ref_field, merch_order_id)
    setattr(receipt, conf.status_field, False)
    receipt.save(update_fields=[conf.ref_field, conf.status_field])
    logger.info(
        'AsiaPay order %s (%s) created for receipt %s',
        merch_order_id,
        trade_type,
        receipt.pk,
    )
    return response


def sync_status(receipt):
    """Re-read the order from AsiaPay and apply it to ``receipt``.

    Returns AsiaPay's ``biz_content`` (``order_status``, ``trans_id``, ...).
    """
    merch_order_id = getattr(receipt, get_conf().ref_field)
    if not merch_order_id:
        raise ValueError('Receipt has no AsiaPay order reference to sync.')
    payload = get_client().query_order(merch_order_id)
    apply_status(receipt, payload.get('order_status'))
    return payload


@transaction.atomic
def apply_status(receipt, status_value):
    """Complete the receipt once AsiaPay reports ``PAY_SUCCESS``.

    Idempotent: the hook runs only on the pending -> completed transition, so
    a callback and a status poll arriving together apply the payment once.
    """
    conf = get_conf()
    if status_value != PAY_SUCCESS:
        return receipt

    # re-read under a lock: the callback and the client's poll race each other
    receipt = (
        type(receipt).objects.select_for_update().filter(pk=receipt.pk).first()
        or receipt
    )
    if getattr(receipt, conf.status_field):
        return receipt

    setattr(receipt, conf.status_field, True)
    receipt.save(update_fields=[conf.status_field])
    _run_hook(conf.on_completed, receipt, status_value)
    logger.info('AsiaPay payment for receipt %s marked completed', receipt.pk)
    return receipt


def handle_callback(merch_order_id):
    """Handle AsiaPay's ``notify_url`` call for ``merch_order_id``.

    Returns the receipt, or ``None`` when no receipt carries that order.
    """
    conf = get_conf()
    receipt = (
        get_receipt_model().objects.filter(**{conf.ref_field: merch_order_id}).first()
    )
    if receipt is None:
        logger.warning('AsiaPay callback for unknown order %s', merch_order_id)
        return None

    payload = sync_status(receipt)
    logger.info(
        'AsiaPay callback order=%s verified=%s',
        merch_order_id,
        payload.get('order_status'),
    )
    return receipt


def _default_title(receipt):
    invoice = getattr(receipt, 'invoice_no', None)
    return f'Payment {invoice}' if invoice else f'Payment #{receipt.pk}'


def _run_hook(dotted_path, receipt, status_value):
    if not dotted_path:
        return
    try:
        import_string(dotted_path)(receipt, status_value)
    except Exception:  # a hook must never break the payment flow
        logger.exception('AsiaPay payment hook %s failed', dotted_path)
