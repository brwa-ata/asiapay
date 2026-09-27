"""The service: a receipt goes pending, and only AsiaPay's answer completes it."""

from decimal import Decimal

import pytest
from django.core.exceptions import ImproperlyConfigured

from asiapay import conf, service

from . import hooks
from .testapp.models import Receipt

pytestmark = pytest.mark.django_db


def start(receipt, **kwargs):
    kwargs.setdefault('trade_type', 'Checkout')
    return service.start_payment(
        receipt,
        notify_url='https://shop.example/api/asia-pay/callback/',
        redirect_url='https://shop.example/return',
        **kwargs,
    )


def test_starting_a_payment_leaves_the_receipt_pending(asiapay):
    receipt = Receipt.objects.create(
        amount=Decimal(25000), invoice_no='RV7', is_completed=True
    )

    payment = start(receipt)

    receipt.refresh_from_db()
    assert payment['redirect_url'] == 'https://pay.example/paygate?x=1'
    assert receipt.is_completed is False
    assert receipt.ref_no.startswith(f'TEST{receipt.pk}T')

    args, kwargs = asiapay.create_order.call_args
    assert args == (receipt.ref_no, Decimal(25000))
    assert kwargs['title'] == 'Payment RV7'
    assert kwargs['trade_type'] == 'Checkout'
    assert kwargs['timeout_express'] == '30m'


def test_the_amount_can_differ_from_the_receipt(asiapay):
    """E.g. the receipt's amount plus a fee the customer pays on top."""
    receipt = Receipt.objects.create(amount=Decimal(10000))

    start(receipt, amount=Decimal(10250), title='Order 9')

    args, kwargs = asiapay.create_order.call_args
    assert args[1] == Decimal(10250)
    assert kwargs['title'] == 'Order 9'


def test_the_order_prefix_is_a_setting(asiapay, settings):
    settings.ASIA_PAY_ORDER_PREFIX = 'SHOP'
    conf.reset()
    receipt = Receipt.objects.create(amount=1)

    start(receipt)

    receipt.refresh_from_db()
    assert receipt.ref_no.startswith(f'SHOP{receipt.pk}T')


def test_an_unpaid_order_stays_pending(asiapay):
    receipt = Receipt.objects.create(amount=1, ref_no='TEST1T1')

    service.sync_status(receipt)

    receipt.refresh_from_db()
    assert receipt.is_completed is False
    assert hooks.calls == []


def test_a_paid_order_completes_the_receipt_and_runs_the_hook_once(asiapay):
    asiapay.query_order.return_value = {'order_status': 'PAY_SUCCESS'}
    receipt = Receipt.objects.create(amount=1, ref_no='TEST1T1')

    service.sync_status(receipt)
    service.sync_status(receipt)
    service.handle_callback('TEST1T1')

    receipt.refresh_from_db()
    assert receipt.is_completed is True
    assert hooks.calls == [(receipt.pk, 'PAY_SUCCESS')]


def test_a_failing_hook_does_not_lose_the_payment(asiapay, settings):
    settings.ASIA_PAY_ON_PAYMENT_COMPLETED = 'tests.hooks.explode'
    conf.reset()
    asiapay.query_order.return_value = {'order_status': 'PAY_SUCCESS'}
    receipt = Receipt.objects.create(amount=1, ref_no='TEST1T1')

    service.sync_status(receipt)

    receipt.refresh_from_db()
    assert receipt.is_completed is True


def test_a_receipt_without_an_order_cannot_be_synced(asiapay):
    with pytest.raises(ValueError):
        service.sync_status(Receipt.objects.create(amount=1))


def test_an_unknown_order_is_ignored(asiapay):
    assert service.handle_callback('NOPE') is None
    asiapay.query_order.assert_not_called()


def test_the_receipt_model_must_be_named(settings):
    settings.ASIA_PAY_RECEIPT_MODEL = ''
    conf.reset()

    with pytest.raises(ImproperlyConfigured):
        service.get_receipt_model()
