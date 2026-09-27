"""The public callback: it names an order, AsiaPay decides what happened."""

import jwt
import pytest

from asiapay.exceptions import AsiaPayAPIError

from .conftest import PRIVATE_KEY
from .testapp.models import Receipt

pytestmark = pytest.mark.django_db

CALLBACK_URL = '/api/asia-pay/callback/'


@pytest.fixture
def receipt():
    return Receipt.objects.create(amount=1, ref_no='TEST1T1')


def post(client, body):
    return client.post(CALLBACK_URL, body, content_type='application/json')


def completed(receipt):
    receipt.refresh_from_db()
    return receipt.is_completed


@pytest.mark.parametrize(
    'body',
    [
        {'biz_content': {'merch_order_id': 'TEST1T1'}},
        {'merch_order_id': 'TEST1T1'},
        {'sign': jwt.encode({'merch_order_id': 'TEST1T1'}, PRIVATE_KEY)},
    ],
    ids=['in biz_content', 'flat', 'in a signed body'],
)
def test_the_order_is_found_wherever_asiapay_puts_it(client, asiapay, receipt, body):
    asiapay.query_order.return_value = {'order_status': 'PAY_SUCCESS'}

    response = post(client, body)

    assert response.status_code == 200
    assert response.json()['result'] == 'SUCCESS'
    asiapay.query_order.assert_called_once_with('TEST1T1')
    assert completed(receipt)


def test_the_callback_trusts_asiapay_not_the_body(client, asiapay, receipt):
    body = {'merch_order_id': 'TEST1T1', 'order_status': 'PAY_SUCCESS'}

    post(client, body)

    assert not completed(receipt)


def test_a_sign_made_with_another_key_names_nothing(client, asiapay, receipt):
    forged = jwt.encode(
        {'merch_order_id': 'TEST1T1'}, 'someone-else-with-their-own-long-key'
    )

    response = post(client, {'sign': forged})

    assert response.status_code == 400
    asiapay.query_order.assert_not_called()


def test_the_callback_also_answers_get(client, asiapay, receipt):
    asiapay.query_order.return_value = {'order_status': 'PAY_SUCCESS'}

    response = client.get(CALLBACK_URL, {'merch_order_id': 'TEST1T1'})

    assert response.status_code == 200
    assert completed(receipt)


def test_a_callback_without_an_order_is_refused(client, asiapay):
    assert post(client, {}).status_code == 400


def test_an_unknown_order_is_acknowledged(client, asiapay):
    """So AsiaPay stops retrying an order this server never created."""
    assert post(client, {'merch_order_id': 'NOPE'}).status_code == 200


def test_asiapay_being_down_asks_for_a_retry(client, asiapay, receipt):
    asiapay.query_order.side_effect = AsiaPayAPIError('down')

    response = post(client, {'merch_order_id': 'TEST1T1'})

    assert response.status_code == 503
    assert not completed(receipt)
